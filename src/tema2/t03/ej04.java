package tema2.t03;

import java.util.Scanner;

public class ej04 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("ingresa la base");
        double base = sc.nextDouble();
        System.out.println("ingresa la altura");
        double altura = sc.nextDouble();
        sc.close();

        double area = AreaRectangulo(base, altura);

        System.out.println("El area del Rectangulo es: " + area);
    }

    static double AreaRectangulo(double base, double altura) {
        return base * altura;
    }
}
