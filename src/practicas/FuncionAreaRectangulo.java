package practicas;

import java.util.Scanner;

public class FuncionAreaRectangulo {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Dame la base");
        double base = sc.nextDouble();
        System.out.println("Dame la altura");
        double altura = sc.nextDouble();
        double area = CalcularArea(base, altura);

        System.out.println("El area del rectangulo es: " + area);
    }

    static double CalcularArea(double base, double altura) {
        return base * altura;
    }
}
