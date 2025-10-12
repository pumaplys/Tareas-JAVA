package tema1.t01;

import java.util.Scanner;

public class ej06 {
    public static void main(String[] args) {
        Scanner input = new Scanner(System.in);

        System.out.println("Digite un numero entero:");
        int N = input.nextInt();
        input.nextLine();

        System.out.println("Digite un numero decimal:");
        double A = input.nextDouble();
        input.nextLine();

        System.out.println("Digite un Caracter:");
        char C = input.next().charAt(0);

        double suma = N+A;
        double resta = A-N;

        System.out.println("Numero Entero: "+N);
        System.out.println("Numero Decimal: "+A);
        System.out.println("Caracter: "+C);
        System.out.println(N+"+"+A+"= "+suma);
        System.out.println(A+"-"+N+"= "+resta);
        System.out.println("El valor numerico de la variable es: "+(int) C);
    }
}
