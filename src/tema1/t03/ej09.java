package tema1.t03;

import java.util.Scanner;

public class ej09 {
    public static void main(String[] args) {
        Scanner sc = new Scanner(System.in);

        System.out.println("Introduce tu peso en kg");
        double peso = sc.nextDouble();

        double pl = peso * 1.62;

        System.out.println("El peso de usted en la luna es de " + pl + "kg");
    }
}
